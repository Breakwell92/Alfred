"""Shared fakes and database fixtures for the budget tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import StaticPool

from alfred_server.budget.prices import Price, PriceUnavailableError
from alfred_server.budget.service import BudgetService
from alfred_server.budget.spend import SpendRow, SpendUnavailableError
from alfred_server.budget.store import BudgetStore
from alfred_server.models.gateway import LOCAL_ALIASES, ModelAlias

# Not ALFRED_-prefixed, so the autouse settings fixture leaves it alone (see test_db.py).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# $2 / $10 per million tokens, like the ADR 0005 defaults.
CLOUD_PRICE = Price(2e-6, 1e-5)


def memory_engine() -> Engine:
    """In-memory SQLite shared across connections."""
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def file_engine(path: Path) -> Engine:
    return create_engine(f"sqlite:///{path}")


@pytest.fixture
def postgres_engines() -> Iterator[Callable[[], Engine]]:
    """A factory of engines on `TEST_DATABASE_URL`, all confined to one throwaway schema."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    schema = f"budget_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engines: list[Engine] = []

    def make() -> Engine:
        engine = create_engine(
            TEST_DATABASE_URL, connect_args={"options": f"-csearch_path={schema}"}
        )
        engines.append(engine)
        return engine

    try:
        yield make
    finally:
        for engine in engines:
            engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


class FakeSpend:
    """`SpendSource` over a list of rows; counts reads; can be made to fail."""

    def __init__(self, rows: list[SpendRow] | None = None) -> None:
        self.data = list(rows or [])
        self.reads = 0
        self.fail = False

    def add(self, usd: float, at: datetime, alias: str = "cloud-claude", **tags: Any) -> None:
        self.data.append(
            SpendRow(
                start_time=at,
                usd=usd,
                alias=alias,
                agent=tags.get("agent"),
                task_id=tags.get("task_id"),
                trigger=tags.get("trigger"),
            )
        )

    def rows(self, start: datetime, end: datetime) -> list[SpendRow]:
        self.reads += 1
        if self.fail:
            raise SpendUnavailableError("spend logs down")
        return [r for r in self.data if start <= r.start_time < end]


class FakePrices:
    def __init__(self, prices: dict[str, Price] | None = None) -> None:
        self.prices = (
            prices
            if prices is not None
            else {a: CLOUD_PRICE for a in ("cloud-claude", "cloud-gpt", "cloud-gemini")}
        )

    def price(self, alias: ModelAlias) -> Price:
        if alias in LOCAL_ALIASES:
            return Price(0.0, 0.0)
        if alias not in self.prices:
            raise PriceUnavailableError(alias)
        return self.prices[alias]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def make_service(
    *,
    spend: FakeSpend | None = None,
    prices: FakePrices | None = None,
    store: BudgetStore | None = None,
    clock: Clock | None = None,
    usd_to_gbp: float = 1.0,
) -> BudgetService:
    return BudgetService(
        store or BudgetStore(memory_engine()),
        spend if spend is not None else FakeSpend(),
        prices or FakePrices(),
        usd_to_gbp=usd_to_gbp,
        timezone="Europe/London",
        clock=clock or Clock(NOW),
    )
